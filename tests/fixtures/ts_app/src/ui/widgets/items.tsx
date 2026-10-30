import { Component } from 'react';

export interface ItemProps {
  label: string;
}

export class Item extends Component<ItemProps> {
  render() {
    return <li>{this.props.label}</li>;
  }
}

export function Badge() {
  return <span />;
}
