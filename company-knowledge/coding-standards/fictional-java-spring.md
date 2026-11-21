# Fictional Java Spring Boot Coding Standards

These standards apply to the fictional `fictional-auth-platform` repository.

## Spring conventions

- Keep controllers thin.
- Use typed request and response models.
- Return problem details for validation failures.
- Keep authentication policy in dedicated services.
- Use constructor injection.
- Do not log credentials, tokens, secrets, or refresh values.

## Testing

- Test authentication success and failure paths.
- Test token expiration and refresh rotation.
- Test authorization boundaries.
- Use fictional credentials only.
- Never commit real secrets or private user data.
