# Releases

The live site runs this repo's latest release (the image `:release`), not `main`: pushes to `main` publish `:main`,
which nothing runs. A release is a tag `v0.MINOR.PATCH` on `main` with an entry in `CHANGELOG.md` that starts with
one sentence: what can a reader do now that they could not before?

```sh
# CHANGELOG.md: "Unreleased" -> "## v0.X.0 (YYYY-MM-DD)", commit to main, then
git tag -a v0.X.0 -m "v0.X.0" && git push origin v0.X.0   # CI publishes :0.X.0 and :release
```

It goes live with the next nightly run on server-jan. The whole procedure, rollback included, is in
bundestag-mdb-cards `docs/release.md`.
