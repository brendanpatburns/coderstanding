#include "shapes.h"
#include "util.h"

namespace geo {

double Square::area() const
{
  return util::clamp(corner_->x * 1.0);
}

double area_of(const IShape &shape)
{
  return shape.area();
}

}  // namespace geo
