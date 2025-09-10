- Use `hatch` for interacting with the project's building and environment configuration
- Use `pre-commit run` to include all lint checks and auto fixes
- Automatically work to resolve failures in the pre-commit output
- Do not include excessive emoji in readme, contributing, and other documentation files
- Use pytest parametrization over subtests

## Testing instructions
- Find the CI plan in the .github/workflows folder.
- From the package root you can just call `hatch test` or `pytest -x`. The commit should pass all tests before proceeding
- Add or update tests for the code you change, even if nobody asked.
