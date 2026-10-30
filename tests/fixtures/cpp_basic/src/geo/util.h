#pragma once
namespace geo::util {
double clamp(double value);
}
extern "C" {
void log_msg(const char *text);
}

namespace geo {
namespace detail {
template<typename T, int N> class VecBase {};
}
using detail::VecBase;
using Vec2 = VecBase<double, 2>;
}
typedef struct OpaqueHandle OpaqueHandle;
OpaqueHandle *handle_new(void);
