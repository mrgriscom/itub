
function UIModel() {
    this.setpoint = ko.observable();
    this.temp = ko.observable();
    this.temp_as_of = ko.observable();
    this.heater_on = ko.observable();
    this.setpoints = ko.observableArray();
    this.temp_tol = ko.observable();
    this.const = null;
    
    var model = this;
    this.setpointChanged = function(obj, event) {
        if (event.originalEvent) { // user changed
            CONN.send(JSON.stringify({action: 'setpoint', value: model.setpoint()}));
        } else { // program changed
            // ignore
        }
    }

    this.update = function(data) {
        if (data.constants) {
            model.const = data.constants;
            model.const.setpoints.unshift(model.const.setpoint_off);
            model.const.setpoints.push(model.const.setpoint_max);
            model.setpoints(model.const.setpoints);
            model.temp_tol(model.const.tolerance);
        }

        model.setpoint(data.setpoint);
        model.temp(data.cur_temp);
        model.heater_on(data.heater_on);
        model.temp_as_of(data.temp_as_of);
    }
    
    this.now = ko.observable(new Date());
    setInterval(function() {
	    model.now(new Date());
    }, 1000);
    this.temp_age = ko.computed(function() {
        model.now();
        var ts = moment(model.temp_as_of());
        return ts.fromNow();
    });
    this.temp_stale = ko.computed(function() {
        return model.temp_as_of() == null || (model.now() - new Date(model.temp_as_of())) / 1000. > model.const.staleness;
    });
}

function format_setpoint(n) {
    if (n == 0) {
        return 'OFF';
    } else if (n == 99) {
        return 'MAX';
    } else {
        return n.toFixed(1) + ' \xb0C';
    }
}

function connect(model, mode) {
    var secure = window.location.protocol.startsWith('https');
    if (secure && window.location.host.startsWith('localhost')) {
	    alert("chrome doesn't support secure websockets to 'localhost'; use an actual IP address");
    }
    
    var conn = new WebSocket((secure ? 'wss' : 'ws') + '://' + window.location.host + '/socket/');
    $('#connectionstatus').text('connecting to server...');
    
    var connectionLost = function() {
	    $('#connectionstatus').text('connection to server lost; reload to reconnect');
	    $('#ui').css('opacity', .65);
	    $('#connectionstatus').css('font-weight', 'bold');
	    scrollTo(0, 0);
    }
    
    conn.onopen = function () {
	    $('#connectionstatus').text('');
    };
    conn.onclose = function() {
	    connectionLost();
    };
    conn.onerror = function (error) {
        console.log('websocket error ' + error);
	    connectionLost();
    };
    conn.onmessage = function (e) {
	    console.log('receiving msg');
        var data = JSON.parse(e.data);
	    console.log(data);
        model.update(data);
    };
    CONN = conn;
}

function init() {
    _init('main');
}

function _init(mode) {
    var model = new UIModel();
    ko.applyBindings(model);
    connect(model, mode);
    MODEL = model;
}

