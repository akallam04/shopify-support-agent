# Site audit

An audit of the live demo site before the Phase 6 upgrade, and what changed. The site was checked
on October 5, 2026 at 1440 by 900 (desktop) and 375 by 812 (phone) in a browser, with a script
that computes text contrast and control sizes from the rendered page, and by hand for keyboard use
and layout.

## Before

### Design critique

The first thing a visitor saw was a storefront. On a phone, the first screen was a decorative
mountain illustration; the assistant, which is the point of the demo, sat behind a round button
in the corner.

| Finding | Severity | Change |
|---|---|---|
| On a phone, the hero illustration filled the first screen and the assistant was hidden behind a launcher. | Critical | Copy and a "Try the assistant" button lead on phones; the illustration is hidden below 480px and shortened below 860px. "Things to try" moved above the product grid. |
| Every example question was read-only. Nothing showed the agent cancelling, changing, or returning an order, or the confirmation step. | Critical | Scenario buttons for tracking, cancelling, changing an address, returning, a final-sale refusal, and an injection attempt, each with a real test order and a note on what to watch. The test order table covers every write path. |
| The confirmation was plain text ending in "Please reply yes or no." | Moderate | A confirmation card shows the exact change with "Yes, go ahead" and "No, keep it" buttons that send the reply. Once answered it shows "Confirmed", "Not changed", or "Replaced by your next message". |
| The only sign of the sandbox was a 10px footnote. | Moderate | A bar under the chat header says the store is a sandbox copy and changes stay in the chat. |
| No way to see what the agent did, and no link to how it was tested. | Moderate | An "Inside the agent" switch shows each turn's graph path with timings, tool calls and results, gate steps, the grounding check, tokens, cost, and release. A How it works page covers the design and the results. |
| The status line said "Online, replies in seconds" even during a cold start of several seconds. | Moderate | The page calls the free `/health` endpoint on load. The status reads "Waking up" until it answers, and a slow first reply says the assistant is waking up. |
| The chat opened on its own on every screen size, covering the product grid on desktop, and played a sound. | Minor | It opens on its own only on wide screens, only on the first visit, without taking focus. The sound is gone. |
| Generic styling: an all-caps eyebrow label, a gradient-filled phrase in the headline, glassy gradients on buttons and badges. | Minor | One accent color, solid buttons, plain headline, and a topographic map illustration that fits an outdoor store. |
| Shared links showed no image or description. | Minor | Description, Open Graph, and Twitter tags on both pages, and a 1200 by 630 share image. |

### Accessibility review (WCAG 2.1 AA)

| # | Issue | Criterion | Severity | Change |
|---|---|---|---|---|
| 1 | Gray helper text at 2.6:1 on the page background (product count, footer, test order note). | 1.4.3 Contrast | Critical | All text now at least 4.5:1; the lightest text color is 4.88:1 on the page background. |
| 2 | White text on the gradient buttons and badges, about 2.9:1 at the lighter end. | 1.4.3 Contrast | Critical | Solid buttons at 5.28:1. |
| 3 | The chat footnote at 10px and 2.95:1; example card hints at 2.95:1; the teal Support link at 2.98:1. | 1.4.3 Contrast | Major | Removed or recolored; the smallest text is 12px, and all of it meets 4.5:1. |
| 4 | No focus style on most custom controls. | 2.4.7 Focus Visible | Major | A 3px focus outline on every control. |
| 5 | Chat header buttons 27 by 27px, send button 32px, suggestion and filter chips about 31px tall. | 2.5.5 Target Size | Major | Every control is at least 44px in its smaller dimension, except inline text links. |
| 6 | The chat input used 14px text, so iPhones zoom the page on focus. | 1.4.4 Resize text, usability | Major | 16px input text. |
| 7 | On a phone the chat was a floating card over a page that still scrolled, and focus could move to the hidden page. | 2.4.3 Focus Order | Major | A full-screen sheet on phones, with the page behind it locked and made inert. |
| 8 | Category filters showed the selected one by color only. | 1.3.1, 4.1.2 | Minor | Filters are toggle buttons with `aria-pressed`. |
| 9 | The cart drawer had no focus handling. | 2.4.3 Focus Order | Minor | Focus moves into the drawer and back to the cart button; the page behind is inert while it is open. |
| 10 | No skip link. | 2.4.1 Bypass Blocks | Minor | A skip link on both pages. |

## After

The same checks on the new pages, with the chat open, every trace expanded, and every replay step
shown:

- Text contrast: no element below 4.5:1 (or 3:1 for large text) on either page, at phone and
  desktop sizes, including the dark trace panels and the chart labels.
- Controls: every button, input, select, and switch has an accessible name, and every one is at
  least 44px in its smaller dimension apart from inline text links.
- Layout: no horizontal scrolling at 375px on either page. Wide tables scroll inside their own
  frame.
- Keyboard: every control is reachable in order, Escape closes the chat and the cart, and focus
  returns to the button that opened them.
- Motion: entrance and pulse animations run only when the visitor has not asked for reduced
  motion.

Checked in a desktop browser with phone emulation; not yet checked with a screen reader on a real
phone.
