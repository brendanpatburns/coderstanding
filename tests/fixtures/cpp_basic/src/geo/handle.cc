#include "util.h"
#define SQUARE(x) ((x) * (x))
#define local_log(x) log_msg(x)

struct OpaqueHandle {
  geo::Vec2 position;
};

namespace {
struct Scratch { int n; };
}

OpaqueHandle *handle_new(void)
{
  local_log("new");
  return SQUARE(2) ? nullptr : nullptr;
}
