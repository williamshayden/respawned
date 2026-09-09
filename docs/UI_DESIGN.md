# Respawned shared review interface

Respawned is the confirmed product name. Use it in the current interface and product documentation; it supersedes the Follow-up Engine label in the accepted mockups. The shared layout and behavior remain the design reference. The Python package and CLI use `respawned`; project environment variables use the `RESPAWNED_` prefix. Existing database and volume identities are preserved; the GitHub repository URL is unchanged until a separate repository rename.

The UI is part of the V1 application: prebuilt assets ship in the Python wheel, source distribution, and Docker image and are served by `respawned serve` on port 8000. Node.js is a frontend development dependency. The same interface is used in the optional Vite development server.

The interface implements issue #1 as one shell across contexts. Its visual reference is the accepted 1586 × 992 review-queue mockup: a 262px navigation rail, 487px list, and remaining detail workspace beneath a 112px header. At smaller desktop widths the columns compress; on mobile the list and detail use the same components sequentially.

## Design system

- Canvas and detail: white; sidebar and evidence surface: near-white neutral gray. Primary ink #101827, secondary #506078, borders #e5e8ec, accent #126c70, selected surface #e8f4f3.
- Local Inter variable font; page and contact titles 34px/600, contextual values 23px/600, body and controls 15px, secondary text 14px, section labels 12px. Responsive sizes must preserve hierarchy.
- Space: 4/8/12/16/24/28/32px. Controls and bounded panels use 8px corners; there are no decorative metric cards.
- Shared component families: navigation rows, record rows, context fields, evidence panel, activity timeline, draft editor, decision buttons, feedback, and dialogs.
- Lucide outline icons match the approved line treatment: RefreshCw brand/refresh, NotebookText review, Mail reply, Send outbox, BarChart3 activity, ShieldCheck policy, Search, CalendarDays, CircleCheck, Sparkles, ArrowRight, X, and SkipForward. Brand remains a small loop-arrow glyph.
- Shared visible labels: Review queue, Reply inbox, Outbox, Activity, Policy, Find a record, All channels, Highest priority, Why this follow-up, Recent activity, Draft message, AI draft · Editable, Reject, Skip, Approve to outbox.

## Functional additions required by the issue

Ready for review / All tracked filters, explicit source-freshness text, draft loading/errors/save feedback, connection settings, narrow-screen navigation, and the other four navigation destinations extend the same visual system. No new domain-specific navigation is introduced. The connected mode uses operator authorization; the default demo is clearly labeled and contains only fictional records. Local styling and fixtures do not imply delivery or mailbox connectivity.

Context is supplied as data: title, kind, stage, contact, fields, reason, history, and draft. The client renders unknown kinds using the same components. The engine owns eligibility, classification, scoring, lifecycle, draft validation, version checks, and outbox reservation.
