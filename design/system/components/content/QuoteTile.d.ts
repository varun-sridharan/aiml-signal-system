/**
 * @startingPoint section="Weekly Digest" subtitle="Leader quote tile with monogram and type badge" viewport="700x360"
 */
export interface QuoteTileProps {
  /** Verbatim. Show any truncation with an ellipsis. */
  quote: string;
  /** The full post. */
  href?: string;
  name: string;
  role?: string;
  org?: string;
  date?: string;
  /** POST is the pulse, EXCERPT the substance. */
  kind?: 'POST' | 'EXCERPT';
  why?: string;
  color?: string;
}
export declare function QuoteTile(props: QuoteTileProps): JSX.Element;
