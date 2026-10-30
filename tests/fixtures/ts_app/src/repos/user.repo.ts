import { Repo } from './repo.js';
import { UserEntity } from '@/models/user';
import type { Permission } from '@/models';

export class UserRepo extends Repo<UserEntity> {
  can(permission: Permission | null): boolean {
    return permission !== null;
  }
}
