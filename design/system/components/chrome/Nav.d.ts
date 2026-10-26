export interface NavItem { label: string; href: string; }
export interface NavProps {
  /** Five items, in site order. */
  items: NavItem[];
  /** Label of the page you are on. The only active indicator. */
  current: string;
}
export declare function Nav(props: NavProps): JSX.Element;
