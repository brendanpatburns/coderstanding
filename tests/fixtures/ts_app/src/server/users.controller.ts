import { Controller, Get } from '@nestjs/common';
import { UserRepo } from '@/repos/user.repo';
import { Logger } from '@example/shared';

@Controller('users')
export class UsersController {
  constructor(private readonly repo: UserRepo, private readonly log: Logger) {}

  @Get()
  list() {
    return this.repo.all();
  }
}
