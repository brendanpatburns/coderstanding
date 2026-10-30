import React from 'react';
import UserCard from './UserCard';
import type { User } from '@/models';

export interface UserListProps {
  users: User[];
}

export const UserList: React.FC<UserListProps> = ({ users }) => (
  <ul>
    {users.map((u) => (
      <UserCard key={u.id} user={u} />
    ))}
  </ul>
);
