// A custom Plotly bundle (core + only the trace types this app actually
// renders), replacing plotly.js-cartesian-dist-min. That package's SVG-only
// "scatter" trace was measured to be the dominant cost of the Compare
// workbench slowing down as more series are added -- each point becomes an
// SVG element, and a handful of series with a few thousand points each
// already took hundreds of ms per redraw. `scattergl` renders the same data
// on the GPU via WebGL instead, which is what resultsPlot.ts now asks for.
// One shared import so every chart in the app agrees on the same trace set.
import Plotly from "plotly.js/lib/core";
import bar from "plotly.js/lib/bar";
import scatter from "plotly.js/lib/scatter";
import scattergl from "plotly.js/lib/scattergl";
import violin from "plotly.js/lib/violin";

Plotly.register([scatter, scattergl, bar, violin]);

export default Plotly;
