export type PillLabel = 'DO THIS' | 'KNOW' | 'RADAR' | 'OPEN' | 'IN PROGRESS' | 'HIT' | 'DONE' | 'MISS' | 'TO DO' | 'POST' | 'EXCERPT';
export interface PillProps {
  /** The label text. Known labels pick their own tone. */
  label?: PillLabel | string;
  /** Force a tone from a known label when the text differs. */
  tone?: PillLabel;
  /** Renders the round outlined count variant used inside tabs. */
  count?: number | string;
}
export declare function Pill(props: PillProps): JSX.Element;
