# Fictional Auth Platform Architecture

This is fictional sample documentation for the `fictional-auth-platform`
application. It does not describe a real company system.

## Overview

The fictional platform has three services:

- `auth-gateway`: receives login and token requests.
- `identity-core`: validates fictional users and issues short-lived tokens.
- `audit-sink`: records authentication events for operations review.

```text
Client -> auth-gateway -> identity-core
                        -> audit-sink
```

The gateway is stateless. Identity data and audit events are stored behind
separate internal interfaces.

## Authentication design

The gateway accepts credentials over HTTPS, validates the login shape, and
forwards the request to `identity-core`. `identity-core` verifies the fictional
account, applies account lockout policy, and creates a short-lived access token
and refresh token.

Access tokens expire after fifteen minutes. Refresh tokens expire after eight
hours and must be rotated on use. Passwords are never returned in API responses
or written into application logs.

The sample service does not use the GitHub credential for authentication.
