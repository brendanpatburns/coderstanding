export type UserId = string;

export interface User {
  id: UserId;
  name: string;
}

export class UserEntity implements User {
  id: UserId = '';
  name = '';
}
