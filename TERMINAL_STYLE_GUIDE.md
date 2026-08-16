# Terminal application styling guide

This guide captures the reusable styling lessons from designing Ticker. It is
intended as a starting point for future terminal applications, especially
keyboard-driven Python apps built with curses.

## Design goal

A terminal UI should feel calm, legible, and native to the developer's current
workflow. Styling should clarify structure and state without making the app
feel like a dense desktop interface squeezed into a terminal.

Prefer a small number of consistent visual roles over many decorative colors.
The user should be able to answer three questions immediately:

1. Where am I?
2. What content can I act on?
3. What is selected or focused right now?

## Compose the screen from modules

Use stable regions with one clear responsibility:

```text
Tabs
┌──────────────────────────────┬─────────────────┐
│ Main body                    │ Right sidebar   │
│ Primary list or workspace    │ Related people  │
│                              │ or navigation   │
└──────────────────────────────┴─────────────────┘
Feedback or input bar
Command footer
```

- **Tabs** communicate the active workspace.
- **Main body** holds the primary task and receives most visual space.
- **Right sidebar** contains secondary navigation, not essential content.
- **Input bar** appears as a lightweight prompt for feedback or text input.
- **Footer** documents direct keyboard commands.
- **Header** is optional. Do not reserve space until it has useful,
  non-redundant information.

Make secondary modules responsive. Collapse the sidebar before compressing or
removing primary table content.

## Establish a small visual hierarchy

Use weight, foreground color, and spacing before using background fills.

- Render table headers separately from rows.
- Make headers bold and theme-colored, without an underline.
- Make primary identifiers, such as ticker symbols or names, slightly bolder
  than supporting values.
- In contextual titles, style the view type as the bold theme anchor and keep
  its subject and metadata muted. For example: theme-colored **Portfolio**
  followed by a dim manager name, reporting period, and disclosure status.
- Use dim text for hints, descriptions, placeholders, and secondary metadata.
- Avoid vertical separators between every table column. Alignment and spacing
  usually create a cleaner structure.
- Keep panel borders unlabeled when tabs and content already identify them.

## Assign colors by semantic role

Define a compact palette and reuse it everywhere:

| Role | Purpose |
| --- | --- |
| Base background | Unselected application surface |
| Theme accent | Active tab, table header, command keys |
| Subtle selection | Focused row, column, or community member |
| Positive | Financial gain or successful state |
| Negative | Financial loss or error state |
| Muted | Hints and secondary labels |

Do not give every component a unique color. Reusing semantic roles makes the
interface coherent and makes new modules easier to design.

Terminal themes render palette values differently. Favor small contrast shifts
and always preserve a monochrome fallback using bold, dim, or reverse video.

## Separate focus, selection, and action

These states are related but not interchangeable:

- **Focus** shows where keyboard navigation currently points.
- **Selection** shows the row or item the user is acting on.
- **Applied action** shows persistent state, such as an active sort.

Start with no navigation highlight:

```python
selected_row: int | None = None
focused_column: str | None = None
```

Only show focus after the user presses an arrow key. Moving column focus must
not immediately sort data; require a separate Sort command. Show a sort marker
only after a sort has actually been applied.

Use stable semantic keys to connect navigation and rendering:

```python
Cell("SYMBOL", key="symbol")
Cell("PRICE", key="price")

column_focused = cell.key == focused_column
```

This keeps header focus, body focus, sorting, and column order synchronized.

Use a subtle full-row treatment for row selection and the same visual family
for column focus. Avoid reverse-video column bands, which dominate the table.
At an intersection, preserve semantic gain/loss colors while retaining enough
selection contrast.

## Design tables as data, not strings

Build headers and rows independently from structured cell definitions:

```python
@dataclass
class Cell:
    text: str
    width: int
    alignment: str = "left"
    key: str | None = None
```

This enables different header and row styles while sharing widths, alignment,
focus keys, and clipping behavior. Maintain a single ordered column model for:

- keyboard navigation;
- header construction;
- row construction;
- sorting;
- responsive visibility.

When a metadata column appears in every view, keep it in a consistent location.
For example, Ticker places `ADDED` last in basic, extended, and study views.

## Make the footer teach commands

The footer is a compact command legend, not a status dashboard. Show only
commands available in the current context.

Style each binding as two parts:

```text
a Add    d Remove    v View    s Sort    q Quit
```

- Key: bold and theme-colored.
- Action name: muted.

Do not duplicate view mode, active sort, or other status in the footer when the
body already communicates it. Keep text input out of the normal keyboard path;
open it explicitly, such as `/` for feedback.

## Persist intent, not accidental defaults

Remember choices the user explicitly made, such as the active tab, view, sort
column, and sort direction. Do not present a default ordering as though the user
selected it.

For a naturally ordered list:

- show no initial sort marker;
- insert new items at the top;
- avoid reordering items during unrelated refreshes;
- apply and persist sorting only after an explicit Sort action.

## Curses implementation cautions

Initialize each color pair for a semantic role and centralize its use. A pair
definition should not normally draw anything, but terminal implementations and
themes vary.

During Ticker development, a dedicated pair using black on `COLOR_CYAN` for a
selected community member appeared to pollute the initial highlight/background
in one terminal even when no member was selected. Treat this as a compatibility
warning:

- avoid unnecessary dedicated background colors;
- reuse the established subtle-selection role;
- verify startup with every selection value set to `None`;
- test both 8/16-color and 256-color terminals;
- test with colors disabled;
- prefer `A_BOLD`, `A_DIM`, or `A_REVERSE` as fallbacks.

Do not combine color pairs with bitwise OR. A curses attribute contains only
one color-pair field, so `color_pair(header) | color_pair(selection)` can resolve
to an unintended pair and black out focused cells. Replace the color bits while
preserving non-color attributes such as bold:

```python
attribute = (attribute & ~curses.A_COLOR) | curses.color_pair(selection_pair)
```

Keep the base background and subtle selection close but distinguishable. Exact
256-color values are terminal-dependent, so validate visually rather than
assuming a palette number has a universal appearance.

## Visual QA checklist

Before reusing the style system, verify:

- Nothing looks selected on first open.
- Arrow navigation introduces focus without triggering an action.
- Row and column focus are distinguishable but not intrusive.
- The active tab uses the same theme accent as headers and command keys.
- Header and body alignment remains exact in every view.
- Columns retain consistent order across loading, success, and error rows.
- Long content clips without breaking borders or the footer.
- The sidebar collapses before primary content becomes unusable.
- Gain/loss meaning does not depend solely on color.
- The interface remains understandable without color.
- No color pair changes the untouched startup background unexpectedly.

## Reusable rule of thumb

Use structure for layout, weight for hierarchy, color for meaning, and
background fills only for active interaction.
