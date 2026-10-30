#include "edge.h"
#include "../geo/util.h"

struct Vert { int x; };

typedef struct Mesh {
  struct Vert *verts;
  struct Edge edge;
} Mesh;

void mesh_free(Mesh *mesh)
{
  log_msg("free");
}
