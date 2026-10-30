using Godot;

namespace Game
{
    public partial class Player : CharacterBody3D
    {
        [Signal] public delegate void HitEventHandler(int damage);

        private Health _health;

        public override void _Ready()
        {
            _health = GetNode<Health>("Health");
            var hud = GetNode<Hud>("../Hud");
            var scene = GD.Load<PackedScene>("res://bullet.tscn");
            var data = ResourceLoader.Load("res://data/stats.tres");
        }
    }

    public partial class Health : Node
    {
        public int Value;
    }

    public partial class Hud : Control { }
}
