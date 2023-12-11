import logging
from typing import Dict, no_type_check

import requests

from prometrix.models.prometheus_config import (AzurePrometheusConfig,
                                                CoralogixPrometheusConfig,
                                                PrometheusConfig)


class PrometheusAuthorization:
    bearer_token: str = ""

    @classmethod
    def azure_authorization(cls, config: PrometheusConfig) -> bool:
        if not isinstance(config, AzurePrometheusConfig):
            return False
        return (config.azure_client_id != "" and config.azure_tenant_id != "") and (
            config.azure_client_secret != "" or  # Service Principal Auth
            config.azure_use_managed_id != "" or  # Managed Identity Auth
            config.azure_use_workload_id != ""  # Workload Identity Auth
        )

    @classmethod
    def get_authorization_headers(cls, config: PrometheusConfig) -> Dict:
        if isinstance(config, CoralogixPrometheusConfig):
            return {"token": config.prometheus_token}
        elif config.prometheus_auth:
            return {"Authorization": config.prometheus_auth.get_secret_value()}
        elif cls.azure_authorization(config):
            return {"Authorization": (f"Bearer {cls.bearer_token}")}
        else:
            return {}

    @no_type_check
    @classmethod
    def _get_azure_metadata_endpoint(cls, config: PrometheusConfig):
        return requests.get(
            url=config.azure_metadata_endpoint,
            headers={
                "Metadata": "true",
            },
            data={
                "api-version": "2018-02-01",
                "client_id": config.azure_client_id,
                "resource": config.azure_resource,
            },
        )

    @no_type_check
    @classmethod
    def _post_azure_token_endpoint(cls, config: PrometheusConfig):
        # Try Azure Workload Identity
        with open("/var/run/secrets/azure/tokens/azure-identity-token", "r") as token_file:
            token = token_file.read()
            data = {
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": token,
                "client_id": config.azure_client_id,
                "scope": f"{config.azure_resource}/.default",
            }
        # Fallback to Azure Service Principal
        if not token:
            if config.azure_use_workload_id:
                return {
                    "ok": False,
                    "reason": f"Could not open token file from {token_file}",
                }
            data = {
                "grant_type": "client_credentials",
                "client_id": config.azure_client_id,
                "client_secret": config.azure_client_secret,
                "resource": config.azure_resource,
            }
        return requests.post(
            url=config.azure_token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )

    @classmethod
    def request_new_token(cls, config: PrometheusConfig) -> bool:
        if cls.azure_authorization(config) and isinstance(
            config, AzurePrometheusConfig
        ):
            try:
                if config.azure_use_managed_id:
                    res = cls._get_azure_metadata_endpoint(config)
                else:  # Service Principal and Workload Identity
                    res = cls._post_azure_token_endpoint(config)
            except Exception:
                logging.exception(
                    "Unexpected error when trying to generate azure access token."
                )
                return False

            if not res.ok:
                logging.error(f"Could not generate an azure access token. {res.reason}")
                return False

            cls.bearer_token = res.json().get("access_token")
            logging.info("Generated new azure access token.")
            return True

        return False
