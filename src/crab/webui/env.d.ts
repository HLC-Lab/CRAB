/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

// The cartesian-only bundle ships no types of its own; it has the same API
// surface as the full package minus non-cartesian trace types, so @types/plotly.js
// applies unchanged.
declare module "plotly.js-cartesian-dist-min" {
  import Plotly from "plotly.js";
  export default Plotly;
}
