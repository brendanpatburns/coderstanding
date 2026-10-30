import { Module } from '@nestjs/common';
import { UsersController } from './users.controller';
import { UserRepo } from '../repos/user.repo';

@Module({
  controllers: [UsersController],
  providers: [UserRepo],
})
export class UsersModule {}
