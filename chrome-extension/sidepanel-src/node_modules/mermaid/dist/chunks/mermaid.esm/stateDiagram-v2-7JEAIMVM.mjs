import {
  StateDB,
  stateDiagram_default,
  stateRenderer_v3_unified_default,
  styles_default
} from "./chunk-DGMWVPIP.mjs";
import "./chunk-Z7BGCTMV.mjs";
import "./chunk-CXMX27UD.mjs";
import "./chunk-ADVQR2OT.mjs";
import "./chunk-YJHQES7P.mjs";
import "./chunk-YDMISRBR.mjs";
import "./chunk-YCI7VYJ2.mjs";
import "./chunk-F6UEWYCX.mjs";
import "./chunk-VDGATQR4.mjs";
import "./chunk-PLCLPJVV.mjs";
import "./chunk-ECMXQ4X3.mjs";
import "./chunk-LBQO3HYP.mjs";
import "./chunk-AYHZ2ZZ4.mjs";
import "./chunk-TYR5776D.mjs";
import "./chunk-IPM4HZQ6.mjs";
import "./chunk-AHS5MEEA.mjs";
import "./chunk-X5WJYYCX.mjs";
import {
  __name
} from "./chunk-PTVI3W5X.mjs";

// src/diagrams/state/stateDiagram-v2.ts
var diagram = {
  parser: stateDiagram_default,
  get db() {
    return new StateDB(2);
  },
  renderer: stateRenderer_v3_unified_default,
  styles: styles_default,
  init: /* @__PURE__ */ __name((cnf) => {
    if (!cnf.state) {
      cnf.state = {};
    }
    cnf.state.arrowMarkerAbsolute = cnf.arrowMarkerAbsolute;
  }, "init")
};
export {
  diagram
};
