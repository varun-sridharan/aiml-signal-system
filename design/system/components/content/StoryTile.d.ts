export interface Source { label: string; href: string; primary?: boolean; }
/**
 * @startingPoint section="Weekly Digest" subtitle="Numbered story tile with sources and thumbs" viewport="700x560"
 */
export interface StoryTileProps {
  n: number;
  tag?: 'DO THIS' | 'KNOW' | 'RADAR';
  /** States the consequence, not the event. */
  headline: string;
  /** 1 to 4 topics. */
  hashtags?: string[];
  body: React.ReactNode;
  /** Names the trade-off. */
  why?: string;
  /** A 2 to 4 sentence worked example, inset. */
  tryIt?: string;
  /** For papers. */
  inNinety?: { method?: string; result?: string; caveat?: string };
  tiesTo?: { label: string; href: string };
  /** Exactly one primary. */
  sources?: Source[];
  checked?: boolean;
  fromBacklog?: boolean;
  /** Larger headline and room for a longer body and expert views (children). */
  feature?: boolean;
  /** Section colour. Defaults to the stories hue. */
  color?: string;
  children?: React.ReactNode;
}
export declare function StoryTile(props: StoryTileProps): JSX.Element;
