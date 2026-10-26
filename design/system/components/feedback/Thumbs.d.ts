export interface ThumbsProps {
  /** The enclosing NEWS section's colour; fills the chosen thumb. */
  color?: string;
  value?: 'up' | 'down' | null;
  onChange?: (value: 'up' | 'down' | null) => void;
  reasonPlaceholder?: string;
}
export declare function Thumbs(props: ThumbsProps): JSX.Element;
