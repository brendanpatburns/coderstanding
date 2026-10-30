import { Injectable } from '@angular/core';
import { ApiService } from '@services/api.service';
import { Logger } from '@shared/logger';
import type { User, UserId } from '../models';

@Injectable()
export class UserService {
  private cache = new Map<UserId, User>();

  constructor(private readonly api: ApiService, readonly logger: Logger) {}

  refresh(): Promise<User[]> {
    return this.api.get<User[]>('/users');
  }
}
