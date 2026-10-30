import { useUserStore } from '@/state/useUserStore';
import UserCard from '@/ui/UserCard';
import Anonymous from '@/state/anon';

export default function Profile() {
  const store = useUserStore();
  const helper = new Anonymous();
  return <UserCard user={store.current} />;
}
