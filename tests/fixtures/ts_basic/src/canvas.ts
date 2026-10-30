import { Drawable, Shape } from "./shape";
import * as geometry from "./geometry";
import { Point } from "./geometry";
import { EventEmitter } from "events";
import { bus } from "./bus";

export class Canvas extends Shape implements Drawable {
  private origin: Point;
  private cursor = new Point(0, 0);

  constructor(origin: Point) {
    super();
    this.origin = origin;
    bus.on("resize", this.redraw);
  }

  draw(): void {
    const size = geometry.area(2, 3);
    const spare = new Point(size, size);
    this.origin.x = spare.x;
  }

  redraw(): void {
    this.cursor.x += 1;
    this.cursor.moveBy(1);
  }
}
