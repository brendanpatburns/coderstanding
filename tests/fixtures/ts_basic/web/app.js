const util = require("./util");
const { greet } = require("./util");
const _ = require("lodash");
const { Canvas } = require("../src/canvas");

class App {
  constructor() {
    this.canvas = new Canvas(null);
    this.button = document.getElementById("go");
  }

  start() {
    this.canvas.draw();
    greet("x");
    util.shout("y");
    util.on("ready", () => this.start());
  }
}

module.exports = { App };
