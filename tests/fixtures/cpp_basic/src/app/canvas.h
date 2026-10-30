#pragma once
#include "geo/shapes.h"
#include <memory>

namespace app {
class Renderer;
struct Palette;
class Theme;

class Canvas {
 public:
  void draw(geo::IShape &shape);
 private:
  std::unique_ptr<Renderer> renderer_;
  Palette *palette_;
  geo::Square square_;
  std::shared_ptr<Theme> theme_;
  const Theme &fallback_;
};

class Renderer {
 public:
  void render(double area);
};
struct Palette { int colors[4]; };
class Theme {};
}  // namespace app

namespace app {
struct Layer {
  OpaqueHandle *handle;
  geo::VecBase<int, 3> origin;
};
}
