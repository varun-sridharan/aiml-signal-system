A keyboard-operable ARIA tablist for the Weekly Digest's primary tabs and sticky NEWS sub-tabs.

```jsx
<Tabs value="news" onChange={setTab} items={[{id:'news',label:'News'},{id:'homework',label:'Homework',count:1}]} />
<Tabs variant="sub" value="stories" items={[{id:'stories',label:"This week's stories",count:9,color:'var(--sec-stories)'}]} />
```

- A tab whose section is not built is not passed in at all: no "coming soon" badges.
- Arrow keys, Home and End move focus and selection.
