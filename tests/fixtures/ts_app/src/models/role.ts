export type Role = 'admin' | 'member';

export interface Permission {
  role: Role;
}
