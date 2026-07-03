# Tools

Each tool lives in its own subfolder: `tools/<toolname>/`

## Adding a new tool

1. Create a folder: `tools/<toolname>/`
2. Drop all your source files in there
3. Add a `tools/<toolname>/README.md` describing what it does and how to build it
4. If it needs a GitHub Actions build, add a workflow in `.github/workflows/<toolname>-build.yml`

## Structure example

```
tools/
  my-tool/
    src/          ← source files
    README.md     ← description + build instructions
    build.bat     ← local build script (optional)
```

## Current tools

| Tool | Folder | Language | Status |
|------|--------|----------|--------|
| *(add yours here)* | `tools/<name>/` | — | — |
