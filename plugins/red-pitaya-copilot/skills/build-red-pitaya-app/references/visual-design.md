# Red Pitaya application visual design

Use this design system for new Red Pitaya application interfaces unless the
user explicitly supplies another brand system or asks to preserve an existing
interface unchanged.

## Company palette

Use the five supplied company colors as the application foundation:

| Token | Value | Primary use |
| --- | --- | --- |
| Dark gray | `#2a2a2a` | panels, controls, secondary surfaces |
| Red | `#e72d2f` | brand accent, primary action, fault state |
| Black | `#000000` | page background, diagnostic surfaces |
| White | `#ffffff` | primary text and high-contrast focus rings |
| Light gray | `#ebebec` | secondary text, borders and neutral state |

Derive transparency only from these colors. Do not silently introduce teal,
blue, green or amber into the core theme. Never rely on red alone to carry a
safety meaning: pair it with visible text, an icon or a status label.

## Interface hierarchy

1. Use a black application frame and dark-gray cards.
2. Use red as a controlled accent rather than a full-page background.
3. Put the app name, description, navigation and board-reported state in a
   compact top bar.
4. Put current state and primary controls before logs, charts and history.
5. Distinguish configured, simulated and measured data in visible text.
6. Keep safe stop visually separate from ordinary configuration actions.
7. Show the exact reason beside every disabled control.

## Components

- Use 8-16 px corner radii, thin light-gray borders and restrained shadows.
- Use filled red for the primary action and a red outline or tint for a
  destructive or stop action. Preserve the semantic label; color is secondary.
- Use uppercase, tracked red eyebrow text for small section labels.
- Use white for confirmed neutral/safe status, red for fault, and light gray
  for unknown. Include the status word and a shape indicator.
- Use black monospace surfaces for logs and raw protocol output.
- Keep charts on dark surfaces with light-gray grids and a red primary series.
- Use system fonts only unless the app packages and licenses a font locally.

## Responsive and accessible behavior

- Maintain readable content at 320 px viewport width.
- Collapse multi-column cards to one column before content becomes cramped.
- Provide a visible white focus ring for keyboard navigation.
- Keep normal text contrast at least WCAG AA.
- Add `prefers-reduced-motion` handling for non-essential animation.
- Preserve semantic headings, button labels, status roles and live regions.
- Do not hide required safety or state text in hover-only tooltips.

## Generated template contract

The bundled common template implements these rules in `css/app.css` and
`index.html`. Extend its CSS variables and component classes instead of
replacing the palette ad hoc. Preserve the functional DOM IDs used by
`js/app.js` and rerun the complete app-skill self-test after changing template
markup, styling, icon generation or behavior.
