import type { NavItem } from './Nav';
/**
 * @startingPoint section="Chrome" subtitle="Shared wordmark + five-item nav" viewport="700x120"
 */
export interface SiteHeaderProps {
  /** Site name. Never the page title. */
  wordmark?: string;
  /** Rendered in --accent directly after the wordmark, no space. */
  wordmarkAccent?: string;
  /** Where the wordmark links. Defaults to the Product page. */
  href?: string;
  items: NavItem[];
  current: string;
  sticky?: boolean;
}
export declare function SiteHeader(props: SiteHeaderProps): JSX.Element;
