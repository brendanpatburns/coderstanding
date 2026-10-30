#include "util.h"
#include <cstdio>
static double helper(double v) { return v; }
namespace geo::util {
double clamp(double value) { return helper(value); }
}
void log_msg(const char *text) { printf("%s", text); }
