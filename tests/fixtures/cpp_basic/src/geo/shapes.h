#pragma once
#include <memory>

namespace geo {

class IShape {
 public:
  virtual double area() const = 0;
  virtual ~IShape() = default;
};

class Polygon : public IShape {
 public:
  virtual int sides() const = 0;
 protected:
  int corners_;
};

template<typename T> class Grid {
  T cells_;
};

class Square : public Polygon {
 public:
  struct Corner { int x; };
  double area() const override;
  int sides() const override { return 4; }
 private:
  std::unique_ptr<Corner> corner_;
  Grid<Corner *> grid_;
};

double area_of(const IShape &shape);

}  // namespace geo
