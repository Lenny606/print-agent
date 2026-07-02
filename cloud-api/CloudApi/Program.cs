using Microsoft.EntityFrameworkCore;
using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.
var connectionString = builder.Configuration.GetConnectionString("DefaultConnection") ?? "Data Source=cloud_print.db";
builder.Services.AddDbContext<PrintDbContext>(options =>
    options.UseSqlite(connectionString));

builder.Services.AddHostedService<JobTimeoutWorker>();

var app = builder.Build();

// Ensure the database is created
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<PrintDbContext>();
    db.Database.EnsureCreated();
}

// -------------------------------------------------------------
// Authentication Helper
// -------------------------------------------------------------
async Task<Agent?> AuthenticateAgentAsync(HttpRequest request, PrintDbContext db)
{
    if (!request.Headers.TryGetValue("X-Agent-ID", out var agentIdValues) ||
        !request.Headers.TryGetValue("X-Agent-Secret", out var agentSecretValues))
    {
        return null;
    }

    var agentId = agentIdValues.ToString();
    var agentSecret = agentSecretValues.ToString();

    var agent = await db.Agents.SingleOrDefaultAsync(a => a.Id == agentId && a.Secret == agentSecret);
    if (agent != null)
    {
        agent.LastHeartbeatAt = DateTime.UtcNow;
        await db.SaveChangesAsync();
    }
    return agent;
}

// -------------------------------------------------------------
// REST Endpoints
// -------------------------------------------------------------

// POST /v1/agent/register
app.MapPost("/v1/agent/register", async (RegisterAgentRequest req, PrintDbContext db, IConfiguration config) =>
{
    var expectedToken = config["InstallToken"] ?? "TEST-INSTALL-TOKEN-123";
    if (req.InstallToken != expectedToken)
    {
        return Results.Json(new { error = "Invalid install token" }, statusCode: 401);
    }

    var agentId = Guid.NewGuid().ToString("n");
    var agentSecret = Guid.NewGuid().ToString("n") + Guid.NewGuid().ToString("n");

    var agent = new Agent
    {
        Id = agentId,
        Secret = agentSecret,
        Name = req.AgentName ?? $"Agent-{agentId.Substring(0, 6)}",
        RegisteredAt = DateTime.UtcNow,
        LastHeartbeatAt = DateTime.UtcNow
    };

    db.Agents.Add(agent);
    await db.SaveChangesAsync();

    return Results.Ok(new RegisterAgentResponse(agentId, agentSecret));
});

// GET /v1/edge/config
app.MapGet("/v1/edge/config", async (HttpRequest request, PrintDbContext db) =>
{
    var agent = await AuthenticateAgentAsync(request, db);
    if (agent == null) return Results.Unauthorized();

    var printers = await db.Printers
        .Where(p => p.AgentId == agent.Id)
        .Select(p => new PrinterConfigDto(p.Id, p.Name, p.IpAddress, p.Port, p.PrintType))
        .ToListAsync();

    return Results.Ok(new EdgeConfigResponse(printers));
});

// GET /v1/edge/jobs/poll
app.MapGet("/v1/edge/jobs/poll", async (HttpRequest request, PrintDbContext db) =>
{
    var agent = await AuthenticateAgentAsync(request, db);
    if (agent == null) return Results.Unauthorized();

    // Get non-expired pending jobs for printers mapped to this agent
    var pendingJobs = await db.PrintJobs
        .Where(j => j.AgentId == agent.Id && j.Status == "PENDING" && j.ExpiresAt > DateTime.UtcNow)
        .ToListAsync();

    foreach (var job in pendingJobs)
    {
        job.Status = "DISPATCHED";
        job.UpdatedAt = DateTime.UtcNow;
    }

    if (pendingJobs.Any())
    {
        await db.SaveChangesAsync();
    }

    var response = pendingJobs.Select(j => new JobDto(
        j.Id,
        j.PrinterId,
        j.FileUrl,
        j.ZplData,
        j.PrintType
    )).ToList();

    return Results.Ok(response);
});

// POST /v1/edge/jobs/{jobId}/status
app.MapPost("/v1/edge/jobs/{jobId}/status", async (string jobId, UpdateJobStatusRequest req, HttpRequest request, PrintDbContext db) =>
{
    var agent = await AuthenticateAgentAsync(request, db);
    if (agent == null) return Results.Unauthorized();

    var job = await db.PrintJobs.SingleOrDefaultAsync(j => j.Id == jobId && j.AgentId == agent.Id);
    if (job == null) return Results.NotFound();

    // Status can only transition if not final
    if (job.Status != "PRINTED" && job.Status != "FAILED" && job.Status != "EXPIRED")
    {
        job.Status = req.Status;
        job.ErrorMessage = req.ErrorMessage;
        job.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync();
    }

    return Results.Ok();
});

// POST /v1/edge/discovered-devices
app.MapPost("/v1/edge/discovered-devices", async (List<DiscoveredDeviceDto> devices, HttpRequest request, PrintDbContext db) =>
{
    var agent = await AuthenticateAgentAsync(request, db);
    if (agent == null) return Results.Unauthorized();

    // Remove old records for this agent
    var oldDevices = await db.DiscoveredDevices.Where(d => d.AgentId == agent.Id).ToListAsync();
    db.DiscoveredDevices.RemoveRange(oldDevices);

    // Insert new discovered devices
    foreach (var dev in devices)
    {
        db.DiscoveredDevices.Add(new DiscoveredDevice
        {
            AgentId = agent.Id,
            IpAddress = dev.IpAddress,
            Port = dev.Port,
            DiscoveredAt = DateTime.UtcNow
        });
    }

    await db.SaveChangesAsync();
    return Results.Ok();
});

// -------------------------------------------------------------
// Upstream Endpoints (Mocked for administration and triggering)
// -------------------------------------------------------------

// POST /v1/print
app.MapPost("/v1/print", async (CreatePrintJobRequest req, PrintDbContext db) =>
{
    // Verify printer and agent mapping
    var printer = await db.Printers.SingleOrDefaultAsync(p => p.Id == req.PrinterId && p.AgentId == req.AgentId);
    if (printer == null)
    {
        return Results.BadRequest(new { error = "Printer does not exist or is not associated with this agent" });
    }

    var jobId = Guid.NewGuid().ToString("n");
    var ttl = req.TtlSeconds ?? 300; // 5 minutes default

    var job = new PrintJob
    {
        Id = jobId,
        AgentId = req.AgentId,
        PrinterId = req.PrinterId,
        FileUrl = req.FileUrl,
        ZplData = req.ZplData,
        PrintType = req.PrintType,
        Status = "PENDING",
        CreatedAt = DateTime.UtcNow,
        ExpiresAt = DateTime.UtcNow.AddSeconds(ttl),
        UpdatedAt = DateTime.UtcNow
    };

    db.PrintJobs.Add(job);
    await db.SaveChangesAsync();

    return Results.Ok(new { JobId = jobId });
});

// POST /v1/printers (Admin config helper)
app.MapPost("/v1/printers", async (CreatePrinterRequest req, PrintDbContext db) =>
{
    var agentExists = await db.Agents.AnyAsync(a => a.Id == req.AgentId);
    if (!agentExists)
    {
        return Results.BadRequest(new { error = "Agent not found" });
    }

    var printer = await db.Printers.SingleOrDefaultAsync(p => p.Id == req.Id);
    if (printer != null)
    {
        printer.Name = req.Name;
        printer.IpAddress = req.IpAddress;
        printer.Port = req.Port;
        printer.PrintType = req.PrintType;
        printer.LastSeenAt = DateTime.UtcNow;
    }
    else
    {
        printer = new Printer
        {
            Id = req.Id,
            AgentId = req.AgentId,
            Name = req.Name,
            IpAddress = req.IpAddress,
            Port = req.Port,
            PrintType = req.PrintType,
            Status = "ONLINE",
            LastSeenAt = DateTime.UtcNow
        };
        db.Printers.Add(printer);
    }

    await db.SaveChangesAsync();
    return Results.Ok();
});

// Helper for testing: GET db status
app.MapGet("/v1/debug/status", async (PrintDbContext db) =>
{
    var agents = await db.Agents.ToListAsync();
    var printers = await db.Printers.ToListAsync();
    var jobs = await db.PrintJobs.ToListAsync();
    var devices = await db.DiscoveredDevices.ToListAsync();

    return Results.Ok(new { agents, printers, jobs, devices });
});

app.Run();

// -------------------------------------------------------------
// Database Models and DbContext
// -------------------------------------------------------------
public class PrintDbContext : DbContext
{
    public PrintDbContext(DbContextOptions<PrintDbContext> options) : base(options) { }

    public DbSet<Agent> Agents { get; set; } = null!;
    public DbSet<Printer> Printers { get; set; } = null!;
    public DbSet<PrintJob> PrintJobs { get; set; } = null!;
    public DbSet<DiscoveredDevice> DiscoveredDevices { get; set; } = null!;
}

public class Agent
{
    [Key]
    public string Id { get; set; } = string.Empty;
    public string Secret { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public DateTime RegisteredAt { get; set; }
    public DateTime? LastHeartbeatAt { get; set; }
}

public class Printer
{
    [Key]
    public string Id { get; set; } = string.Empty;
    public string AgentId { get; set; } = string.Empty;
    public string Name { get; set; } = string.Empty;
    public string IpAddress { get; set; } = string.Empty;
    public int Port { get; set; }
    public string PrintType { get; set; } = "PDF"; // PDF or ZPL
    public string Status { get; set; } = "ONLINE";
    public DateTime LastSeenAt { get; set; }
}

public class PrintJob
{
    [Key]
    public string Id { get; set; } = string.Empty;
    public string AgentId { get; set; } = string.Empty;
    public string PrinterId { get; set; } = string.Empty;
    public string? FileUrl { get; set; }
    public string? ZplData { get; set; }
    public string PrintType { get; set; } = "PDF"; // PDF or ZPL
    public string Status { get; set; } = "PENDING"; // PENDING, DISPATCHED, PRINTED, FAILED, EXPIRED
    public DateTime CreatedAt { get; set; }
    public DateTime ExpiresAt { get; set; }
    public DateTime UpdatedAt { get; set; }
    public string? ErrorMessage { get; set; }
}

public class DiscoveredDevice
{
    [Key]
    public int Id { get; set; }
    public string AgentId { get; set; } = string.Empty;
    public string IpAddress { get; set; } = string.Empty;
    public int Port { get; set; }
    public DateTime DiscoveredAt { get; set; }
}

// -------------------------------------------------------------
// DTOs and Payload contracts
// -------------------------------------------------------------
public record RegisterAgentRequest(
    [property: JsonPropertyName("install_token")] string InstallToken,
    [property: JsonPropertyName("agent_name")] string? AgentName
);

public record RegisterAgentResponse(
    [property: JsonPropertyName("client_id")] string ClientId,
    [property: JsonPropertyName("client_secret")] string ClientSecret
);

public record PrinterConfigDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("ip_address")] string IpAddress,
    [property: JsonPropertyName("port")] int Port,
    [property: JsonPropertyName("print_type")] string PrintType
);

public record EdgeConfigResponse(
    [property: JsonPropertyName("printers")] List<PrinterConfigDto> Printers
);

public record JobDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("printer_id")] string PrinterId,
    [property: JsonPropertyName("file_url")] string? FileUrl,
    [property: JsonPropertyName("zpl_data")] string? ZplData,
    [property: JsonPropertyName("print_type")] string PrintType
);

public record UpdateJobStatusRequest(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("error_message")] string? ErrorMessage
);

public record DiscoveredDeviceDto(
    [property: JsonPropertyName("ip_address")] string IpAddress,
    [property: JsonPropertyName("port")] int Port
);

public record CreatePrintJobRequest(
    [property: JsonPropertyName("agent_id")] string AgentId,
    [property: JsonPropertyName("printer_id")] string PrinterId,
    [property: JsonPropertyName("file_url")] string? FileUrl,
    [property: JsonPropertyName("zpl_data")] string? ZplData,
    [property: JsonPropertyName("print_type")] string PrintType,
    [property: JsonPropertyName("ttl_seconds")] int? TtlSeconds
);

public record CreatePrinterRequest(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("agent_id")] string AgentId,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("ip_address")] string IpAddress,
    [property: JsonPropertyName("port")] int Port,
    [property: JsonPropertyName("print_type")] string PrintType
);

// -------------------------------------------------------------
// Background worker for expiring stale print jobs
// -------------------------------------------------------------
public class JobTimeoutWorker : BackgroundService
{
    private readonly IServiceProvider _serviceProvider;
    private readonly ILogger<JobTimeoutWorker> _logger;

    public JobTimeoutWorker(IServiceProvider serviceProvider, ILogger<JobTimeoutWorker> logger)
    {
        _serviceProvider = serviceProvider;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                using var scope = _serviceProvider.CreateScope();
                var db = scope.ServiceProvider.GetRequiredService<PrintDbContext>();

                var now = DateTime.UtcNow;
                var expiredJobs = await db.PrintJobs
                    .Where(j => (j.Status == "PENDING" || j.Status == "DISPATCHED") && j.ExpiresAt < now)
                    .ToListAsync(stoppingToken);

                if (expiredJobs.Any())
                {
                    foreach (var job in expiredJobs)
                    {
                        job.Status = "EXPIRED";
                        job.UpdatedAt = now;
                        job.ErrorMessage = "Job expired due to TTL timeout.";
                    }

                    await db.SaveChangesAsync(stoppingToken);
                    _logger.LogInformation("Expired {Count} stale print jobs.", expiredJobs.Count);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error occurred executing JobTimeoutWorker.");
            }

            await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
        }
    }
}
