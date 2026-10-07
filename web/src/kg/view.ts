import type { KgFocus } from './focus'
import type { KgGraph } from './types'

/** Options every view of a graph takes (mountGraph, mountFlow). */
export type KgViewOptions = {
  /** Merge the releases of a study or variable into one node (default true). */
  collapseVersions?: boolean
  /** Show only what two or more concepts share (see bridges/sharedOnly; default false). */
  sharedOnly?: boolean
  /** The user picked something (a node, or a concept × study pair; null: the
   * background). The host decides what to show, and tells other views (focus). */
  onFocus?: (focus: KgFocus | null) => void
}

/** What every view of a graph can do, so a host can switch views without caring
 * which one is showing. Ids are as drawn (collapsed or not). */
export type KgView<O extends KgViewOptions = KgViewOptions> = {
  /** Draw another graph, or the same with changed options. */
  update(graph: KgGraph, options?: Partial<O>): void
  /** Change options for the current graph. */
  setOptions(options: Partial<O>): void
  /** Show a focus picked elsewhere (another view, a list): highlight what it
   * connects (see focusConnections), fade the rest; null clears. Doesn't call onFocus. */
  focus(focus: KgFocus | null): void
  /** Call after the container changed size (e.g. was hidden, then shown). */
  resize(): void
  /** Re-read the --kg-* colours (e.g. after a theme change). */
  refreshStyle(): void
  destroy(): void
}
