import { Component } from 'react';
import { Widgets } from './widgets';
import { UserList } from './UserList';
import * as Models from '@/models';
import Profile from './Profile';

interface DashboardState {
  users: Models.User[];
}

export default class Dashboard extends Component<{}, DashboardState> {
  render() {
    return (
      <section>
        <Widgets.Item label="x" />
        <UserList users={this.state.users} />
        <Profile />
      </section>
    );
  }
}
