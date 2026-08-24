import {
  createFlowDiagram,
  styles_default
} from "./chunk-RHFEMEQ7.mjs";
import "./chunk-5VM5RSS4.mjs";
import "./chunk-XXDRQBXY.mjs";
import "./chunk-POPQ4Y6H.mjs";
import "./chunk-LNGE3PJU.mjs";
import "./chunk-CKBBP62Z.mjs";
import "./chunk-F27PBJKO.mjs";
import "./chunk-JQ64N6SF.mjs";
import "./chunk-R7TYR2AO.mjs";
import "./chunk-OBVCFTLP.mjs";
import "./chunk-TEH6E4GO.mjs";
import "./chunk-P2QGCYS3.mjs";
import "./chunk-GMAD6QVW.mjs";
import "./chunk-PWAF6VOD.mjs";
import "./chunk-75Z2AOVW.mjs";
import "./chunk-DU6HZSFF.mjs";
import "./chunk-X3CZISLH.mjs";
import {
  __name
} from "./chunk-Y2CYZVJY.mjs";

// src/diagrams/swimlanes/styles.ts
var getStyles = /* @__PURE__ */ __name((options) => `${styles_default(options)}
  .swimlane.cluster rect {
    stroke: ${options.clusterBorder} !important;
  }
  [data-look="neo"].cluster rect {
    filter: none;
  }
`, "getStyles");
var styles_default2 = getStyles;

// src/diagrams/swimlanes/swimlanesDiagram.ts
var diagram = createFlowDiagram({ defaultLayout: "swimlane", styles: styles_default2 });
export {
  diagram
};
