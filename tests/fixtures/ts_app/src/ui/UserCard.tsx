import type { User } from '@/models';

interface UserCardProps {
  user: User;
}

export default function UserCard({ user }: UserCardProps) {
  return <div>{user.name}</div>;
}
