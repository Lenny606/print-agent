import httpx
import logging
from app.config import CLOUD_API_URL, INSTALL_TOKEN, AGENT_NAME, save_credentials

logger = logging.getLogger("PrintAgent.Onboarding")

async def register_agent() -> bool:
    """
    Onboards the agent using the INSTALL_TOKEN.
    Fetches agent ID and Secret from the Central Cloud API and stores them securely.
    """
    if not INSTALL_TOKEN:
        logger.error("INSTALL_TOKEN env variable is not set. Cannot perform onboarding.")
        return False

    register_url = f"{CLOUD_API_URL}/v1/agent/register"
    logger.info(f"Attempting to register agent at {register_url} using Install Token...")

    payload = {
        "install_token": INSTALL_TOKEN,
        "agent_name": AGENT_NAME
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(register_url, json=payload, timeout=10.0)

            if response.status_code == 200:
                data = response.json()
                client_id = data.get("client_id")
                client_secret = data.get("client_secret")

                if client_id and client_secret:
                    success = save_credentials(client_id, client_secret)
                    if success:
                        logger.info("Agent registration successful and credentials stored.")
                        return True
                    else:
                        logger.error("Failed to store credentials locally.")
                else:
                    logger.error("Invalid response payload from registration server.")
            elif response.status_code == 401:
                logger.error("Registration failed: Unauthorized. Invalid Install Token.")
            else:
                logger.error(f"Registration failed with HTTP status code {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Error during agent registration: {e}")

    return False
