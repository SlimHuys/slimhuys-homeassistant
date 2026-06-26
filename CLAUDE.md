# SlimHuys Home Assistant — ontwikkelnotities

## Nieuwe versie uitbrengen

HACS detecteert alleen GitHub releases, niet losse commits of tags. Altijd alle drie stappen uitvoeren:

1. Versienummer ophogen in `custom_components/slimhuys/manifest.json`
2. Committen en pushen naar `main`
3. GitHub release aanmaken:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
```
