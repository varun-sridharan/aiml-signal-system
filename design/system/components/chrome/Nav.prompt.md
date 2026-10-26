The site's five-item nav; use inside SiteHeader, never on its own page bar.

```jsx
<Nav current="Weekly Digest" items={[{label:'Product',href:'Product.html'},{label:'Weekly Digest',href:'Weekly-Digest.html'}]} />
```

- Exactly one item renders active (accent + 1.5px underline). Others are plain soft links with no persistent hover state.
