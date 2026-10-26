---
name: ai-ml-signal-design
description: Use this skill whenever you create or change any page, template, component or visual asset for AI/ML Signal. The brand kit (tokens, components, guidelines) lives in design/system/ at the repository root and is the single source of truth for colours, type, spacing and the shared site header.
user-invocable: true
---

The AI/ML Signal brand kit lives in `design/system/` at the root of this repository. It is the same
design system Varun keeps in the Claude app as "AI/ML Signal", exported on 2026-10-25.

1. Read `design/system/readme.md` first: voice, visual foundations, iconography and layout rules.
2. Colours, type, spacing, radii and motion come only from `design/system/tokens/*.css`. Never
   hard-code a hex value or a font size that a token already defines; if a value is missing, add it
   to the tokens and say so.
3. Reuse the components in `design/system/components/` (SiteHeader, Nav, Tabs, Pill, Hashtags,
   StoryTile, QuoteTile, Thumbs). Each has a `.prompt.md` with its usage rules.
4. Every page uses the one shared site header (Product page style: sticky, translucent, wordmark and
   nav centred over the content), and one of three layouts: Landing, Document with contents
   sidebar, or Edition.
5. For throwaway mocks you may copy assets out into static HTML; for production code, change the
   templates and tokens in this repository.
