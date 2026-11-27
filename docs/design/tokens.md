# Hub design tokens

`custom_components/laundry_hub/frontend/tokens.css` is shared by Laundry Hub and
Pantry Hub. Both apps use the same **structure tokens** and each sets its own
**accent palette**. Approved mockups: "One family, two personalities" (Linen &
Teal for laundry, Harvest & Clay for the pantry).

## Reusing it in Pantry Hub

1. Copy `tokens.css` unchanged into the Pantry Hub frontend.
2. Add a Pantry palette block and point the `--app-*` mapping at it:

```css
:host, :root {
  --ph-accent: #a9521f;          /* Harvest & Clay */
  --ph-accent-pressed: #8a4219;
  --ph-accent-tint: #f6e6da;
  --ph-surface: #faf6f1;
  --ph-border: #eadfd3;
  --ph-sage: #5e7a4f;
  --app-accent: var(--ph-accent);
  --app-accent-pressed: var(--ph-accent-pressed);
  --app-accent-tint: var(--ph-accent-tint);
  --app-surface: var(--ph-surface);
  --app-border: var(--ph-border);
}
```

3. Components use only `--hub-*` and `--app-*`, never `--lh-*` or `--ph-*`.

## Tokens

| Group | Tokens |
|---|---|
| Type | `--hub-font` (Inter → system-ui), `--hub-text-sm/md/lg/xl` = 13/16/20/28 px |
| Spacing | `--hub-space-1…6` = 4/8/12/16/24/32 px |
| Radius | `--hub-radius-control` 12, `--hub-radius-card` 16, `--hub-radius-pill` 999 |
| Touch | `--hub-touch` 44 px minimum |
| Neutrals | `--hub-ink` #1C1B19, `--hub-muted` #5C5953, `--hub-ground` #F6F5F2 |
| Status | `--hub-ok` #2E7D4F, `--hub-warn` #9A6412, `--hub-problem` #B3261E |
| Surfaces | `--hub-card`, `--hub-text`, `--hub-text-muted`, `--hub-divider` follow HA's `--card-background-color`, `--primary-text-color`, `--secondary-text-color`, `--divider-color` |
| Laundry | `--lh-accent` #1F6F8B, `--lh-accent-pressed` #155468, `--lh-accent-tint` #E2EFF3, `--lh-surface` #F3F7F8, `--lh-border` #DCE6E9 |

## Modes

- **Dark:** the host gets a `dark` attribute from `hass.themes.darkMode`. Same
  hues, accent lightened about 15 % (#41859D), surfaces from HA.
- **Follow HA theme colour** (option, off by default): the host gets
  `follow-ha`, and `--app-accent` becomes HA's `--primary-color`.
- Wash-bin colours are user-defined per bin and never replaced by the accent.
