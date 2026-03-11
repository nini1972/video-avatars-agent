

import subprocess
from typing import Dict
from urllib.parse import urlparse

import httpx

from google.adk.agents.readonly_context import ReadonlyContext

from google.auth.credentials import TokenState
from google.auth.transport.requests import AuthorizedSession, Request
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2.id_token import fetch_id_token_credentials

class IdentityTokenHeaderProvider:
    def __init__(self, remote_service_url: str):
        parsed_url = urlparse(remote_service_url)
        self.root_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        self.host = parsed_url.hostname or ""
        self.outside_cloud = False # at first, assume we run in Cloud
        self.session = None

    def __call__(self, context: ReadonlyContext) -> Dict[str, str]:
        headers = {}
        if self.host in {"localhost", "127.0.0.1", "0.0.0.0"}:
            return headers
        if not self.outside_cloud:
            try:
                if not self.session:
                    credentials = fetch_id_token_credentials(
                        audience=self.root_url,
                    )
                    credentials.refresh(Request())
                    self.session = AuthorizedSession(
                        credentials
                    )
                if self.session.credentials.token_state != TokenState.FRESH:
                        self.session.credentials.refresh(
                            Request()
                        )
                id_token = self.session.credentials.token
            except DefaultCredentialsError:
                self.outside_cloud = True
        if self.outside_cloud: # Not elif because outside_cloud might change
            # Local run, fetching authenticated user's identity token
            # from gcloud CLI
            id_token = subprocess.check_output(
                [
                    "gcloud",
                    "auth",
                    "print-identity-token",
                    "-q"
                ]
            ).decode().strip()
        if id_token:
            headers["Authorization"] = f"Bearer {id_token}"
        return headers
