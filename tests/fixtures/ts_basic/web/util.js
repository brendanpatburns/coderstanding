function greet(name) {
  return "hi " + name;
}

function shout(name) {
  return greet(name).toUpperCase();
}

module.exports = { greet, shout, on() {} };
