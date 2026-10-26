The per-story feedback control: two icon buttons, then a one-line reason box only after thumbs down.

```jsx
<Thumbs color="var(--sec-stories)" onChange={v => save(v)} />
```

- aria-labels are "Useful" and "Not useful"; the choice is UI state, not a slot.
