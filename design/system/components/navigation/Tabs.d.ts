export interface TabItem {
  id: string;
  label: string;
  /** Shown in an outlined pill after the label. */
  count?: number | string;
  /** Section colour for sub-tabs (marker dot and underline). */
  color?: string;
}
/**
 * @startingPoint section="Navigation" subtitle="Primary and sticky sub-tabs, ARIA tablist" viewport="700x150"
 */
export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange?: (id: string) => void;
  variant?: 'primary' | 'sub';
  /** aria-label for the tablist. */
  label?: string;
  /** Content at the right end of the bar, e.g. Expand all / Collapse all. */
  trailing?: React.ReactNode;
}
export declare function Tabs(props: TabsProps): JSX.Element;
