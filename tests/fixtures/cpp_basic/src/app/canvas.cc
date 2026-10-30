#include "canvas.h"
#include "../geo/util.h"
#include <vector>

namespace app {

struct Brush { int size; };

void Canvas::draw(geo::IShape &shape)
{
  Brush *brush = new Brush();
  renderer_->render(geo::area_of(shape));
  log_msg("drawn");
  CHECK(brush != nullptr);
  delete brush;
}

}  // namespace app
