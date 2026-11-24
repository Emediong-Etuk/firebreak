"""Run the official Kaggle CLI behind the session's credential-injecting proxy.

The real KGAT token never enters this container: the network proxy overwrites the
Authorization header on requests to api.kaggle.com. The CLI's startup check sends the
token in a request *body* (IntrospectToken), which the proxy cannot rewrite, so that one
check is bypassed here. Every real API call still authenticates via the header.
"""
import os
import sys

os.environ.setdefault("KAGGLE_API_TOKEN", "proxy-injected")

# Patch at the SDK level, before `import kaggle` (whose __init__ authenticates on import).
from kagglesdk.security.services import oauth_service  # noqa: E402
from kagglesdk.security.types.oauth_service import IntrospectTokenResponse  # noqa: E402


def _introspect_stub(self, request=None):
    resp = IntrospectTokenResponse()
    resp.active = True
    resp.username = os.environ.get("KAGGLE_USERNAME", "proxy-user")
    return resp


oauth_service.OAuthClient.introspect_token = _introspect_stub

from kaggle.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
