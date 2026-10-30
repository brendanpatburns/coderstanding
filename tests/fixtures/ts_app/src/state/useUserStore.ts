import { UserRepo } from '@/repos/user.repo';
import type { User } from '@/models';

type Status = 'idle' | 'loading';

export class UserStore {
  private repo: UserRepo | null = null;
  status: Status = 'idle';
  current!: User;
}

export function useUserStore(): UserStore {
  return new UserStore();
}
